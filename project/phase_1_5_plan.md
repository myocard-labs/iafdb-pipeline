# iafdb-pipeline — Phase 1.5 implementation plan

**Repo:** iafdb-pipeline · **Phase:** 1.5
**Phase design doc:** `intracardiac-platform/phases/phase_1_5/design.md`
**Status:** planning · **Progress:** 0/1 detailed steps done
**Repo estimate:** **1–2 h active** (S0, the egm-signal v0.3.0 adoption) — the only item scoped so
far. IAF1 · IAF2 · B11 · B20 and the `theory.md` trim are listed in Scope but **not yet broken into
steps or estimated**; that flow-down is pending Daniel's go-ahead. Cold-start estimates are by
analogy — `estimation_ledger.csv` is empty, so there is no rate to multiply by. Ranges are wide.

**Wave placement:** S0 touches no schema and is gated only on egm-signal v0.3.0, so it sits **outside
the Wave-1 re-pin cascade** and runs early, parallel to Wave 1. B20 and B11's schema half are
**Wave 1** (they need egm-contracts v0.6.0 + egm-data). IAF1 is **Wave 2** — it needs SIG1
(egm-signal v0.4.0) *and* the `T` + position-range values that study §8.1 produces.

---

## Scope — what this plan covers

| Phase item | What it needs from this repo | Steps |
|---|---|---|
| *(no id yet)* | **egm-signal v0.3.0 adoption** — `DEFAULT_TARGET_QRS_PP_MV` is deleted upstream and `RWaveAnchoring`'s target becomes required. Drop the import, make `export_bank`'s `target_qrs_pp_mv` required, re-pin, and refresh the doc passages that describe the old two-layer default. Originated here (the `theory.md` §7.1 finding); value ruled by research, structural fix ruled by Daniel 2026-07-28. **The project-lead may want to give this a `B` id** so it appears in design §4 / §6 rather than floating. | S0 |
| **IAF1** | Activation-train splitting — detect the train per channel, emit length-`T` windows at fractional position `p ∼ 𝒫`, drop boundary + multi-beat windows, filter per record first. **Response only**: the detector, the anchor-window helper, and the boundary / multi-beat predicates are SIG1's; this repo does not re-implement the crop math. | *pending flow-down* |
| **IAF2** | `patient-id-as-label` policy — a `format.label_policy` key + a closure, matching the shipped `all-healthy` / `unlabeled` shape. Feeds the §8.10 patient-shortcut-hunt study. | *pending flow-down* |
| **B11** | Per-record audit report — optional `--report PATH` JSON sidecar (median QRS p-p, calibration scalar, threshold applied, windows kept vs rejected, winning surface lead) + stamping `iafdb_bank` 1.3's `run_record_path` pointer. Schema half is Wave 1; the report content follows. | *pending flow-down* |
| **B20** | `noise_bank` `bank_id` HDF5 root attr — moves the id off the run-record sidecar onto the bank itself. Wave 1, additive. | *pending flow-down* |
| *(doc)* | **`theory.md` trim to consumption-only** — §1.1–1.3 and §2.1 graduate to egm-signal's new `docs/theory.md`; this doc keeps composition + the IAFDB-specific parts and cross-links down. **Must land together with egm-signal's S7a** or the math is briefly orphaned. | *pending flow-down* |

**Not in this plan:** study §8.1 (the IAFDB activation-interval + position-range analysis). Its code
lands in this repo, but per Daniel (2026-07-28) research / the project-lead score and estimate it.
It is tracked here only as the **external dependency that sets IAF1's `T` and `𝒫`**.

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

## Steps

Each step is one focused commit, ends green (`ruff format src tests` + `ruff check` + `mypy src` +
`pytest`), and states its verification. ☐ todo · 🔨 wip · ✅ done

### S0 — Adopt egm-signal v0.3.0: required QRS-calibration target ☐ (1–2 h)

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

### S1+ — IAF1 · IAF2 · B11 · B20 · the `theory.md` trim ☐ — *pending flow-down*

Not yet broken into steps. Awaiting go-ahead before scoping and estimating.

## Complexity + estimate

Scored with the rubric in
`intracardiac-platform/project/investigations/estimate_vs_actual_tracking.md` §8.

| Item | Cx | Size | Estimate | Driver notes |
|---|---|---|---|---|
| egm-signal v0.3.0 adoption | **2** | S | 1–2 h | *Change size:* three source lines + a re-pin. *Novelty:* none — mechanical. *Surface:* small in `src/`, but **14 test call sites** and three doc passages, plus a coordinated adoption of an upstream breaking change. *Verification:* full suite + a confirmation that no artifact changed. Sits at the S anchor (`noise_bank` `bank_id` add); the test churn is what keeps it off XS. Slightly wider than egm-signal's own 0.5–1 h for S0 because the call-site count is on this side. |
| IAF1 · IAF2 · B11 · B20 · doc trim | — | — | *pending* | Scored at flow-down. |

## Effort tracking

Mechanism: `intracardiac-platform/project/investigations/estimate_vs_actual_tracking.md` §7. Daniel
speaks the markers; this chat stamps the time from `date`. **Active = marked span − breaks.**
**Backstop:** unmarked silence > **2 h** = away.

### Session log

| Timestamp (local) | Event | Focus (issue) | Note |
|---|---|---|---|
| — | *(not started)* | — | Planning only; clock starts at the first `start` marker. |

### Effort by issue

| Issue | Task-type | Estimate | Active | Elapsed | Sessions |
|---|---|---|---|---|---|
| egm-signal v0.3.0 adoption | API change / re-pin | 1–2 h | — | — | 0 |
| **Repo total** | | **1–2 h** *(pending flow-down)* | | | |

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
