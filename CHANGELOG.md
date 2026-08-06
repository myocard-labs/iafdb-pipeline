# Changelog

All notable changes to `iafdb-pipeline` are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); the project aims to follow
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- **`noise_bank` `bank_id` on the bank itself** (`noise_bank` 1.1, B20). The stable
  cross-artifact id was previously carried only on the `noise_bank_run_record.json`
  sidecar; it is now also stamped on the HDF5 root attr, so a consumer can identify a
  noise bank without opening the sidecar. Both are fed from one resolution, so they
  cannot disagree.
- **`iafdb_bank` 1.3 fields adopted** (IAF3 / B11a): `run_record_path` (the audit-report
  sidecar pointer) and per-trace `activation_position` (the realized `[0,1]` anchor).
  Both ship **absent** — the `--report` generator that fills the first and the
  activation-aware splitter that fills the second are Phase-1.5 Wave-2 work. Absence is
  the contract's "unknown"; `activation_position` is never defaulted to `0.0`, which is a
  legitimate position value.

- **`unlabeled` label policy** (`format.label_policy: unlabeled`) — the `label_fn` returns
  `None`, leaving every `label_truth` unset; the honest IAFDB policy that feeds an unlabeled
  `upred_` eval (IAFDB has no fibrosis ground truth).
- **Fail-fast `bank_id` validation** — a malformed `data.bank_id` override is now rejected at
  config-load, before the export run, instead of at the write step (Refactor Step 8).

### Changed

- **BREAKING — `export_bank()`'s `target_qrs_pp_mv` is now required** (B22). egm-signal
  v0.3.0 deleted `DEFAULT_TARGET_QRS_PP_MV` on the library-defaults rule, and this
  producer follows: the calibration target decides what scale a whole corpus is
  normalized to, so it is policy, and the one place it is defaulted is the CLI config
  (`cli/_config.py`, `1.0` mV). Previously the library said 1.5 and the CLI said 1.0, so
  the same code calibrated to different scales depending on the entry point. **No stored
  data changes** — every bank on disk came through the CLI, which already passed 1.0.
  *Migration:* pass the value explicitly to `export_bank(...)`; CLI and YAML users are
  unaffected.
- **`docs/theory.md` trimmed to composition + IAFDB specifics.** The band-pass, sliding
  peak-to-peak, threshold-strategy and R-wave-anchoring derivations moved to
  `egm-signal/docs/theory.md` (§1, §6), which absorbed them when SIG1 shipped — the repo
  owning a primitive owns its math, so nothing is now derived in two places. This doc
  keeps what it uniquely knows: the order the primitives are composed in, the pooled
  channel set, the calibration *target* value, the parameters, and the sim-vs-real
  divergence map. Pool assembly went to egm-signal (its empty-pool sentinels are
  meaningless apart from it); the calibration target's value stayed here, so B22's
  single-source fix is not undone in prose.
- **Re-pin `egm-data v0.6.0 → v0.6.1`** — picks up the converter fix so a ClassifierBank
  derived from an `iafdb_bank` emits `patient_id` as `'iaf1'` rather than the Pydantic
  `repr` `"root='iaf1'"` (same for `band_hz`). Verified against the reproduction that
  found it; no iafdb-side change was needed.
- **Re-pin `egm-signal v0.2.0 → v0.4.0`.** Note this is v0.4.0, not the v0.3.0 the B22
  change was written up under — egm-signal's 0.3.0 release was never tagged, and the
  commit shipped inside v0.4.0. v0.4.0 is otherwise purely additive here: every
  threshold, extraction and calibration name this producer imports is unchanged.
- **Re-pin `egm-contracts v0.5.3 → v0.6.0` and `egm-data v0.5.0 → v0.6.0`** — the Phase-1.5
  Wave-1 coordinated bump. Additive for this producer: a bank regenerated with the same
  config is bit-identical to its pre-migration counterpart, so no bank needs regenerating.
- **mypy now type-checks `tests` as well as `src`** (fleet convention). Run bare `mypy`.
- **`.gitignore` output-dir patterns root-anchored** (`/data/`, `/banks/`, …). Unanchored
  they matched at any depth and would have silently untracked a same-named source package.
- Re-pin `egm-contracts v0.5.1 → v0.5.3` and `egm-data v0.4.0 → v0.5.0` (the coordinated
  ArtifactId-date-optional + egm-data pure-I/O cascade).
- Re-pin `egm-signal v0.1.0 → v0.2.0` — align on the current egm-signal (v0.2.0 is purely
  additive; surfaced by the S8-7 integration smoke test, which installs one consistent
  egm-signal across the whole constellation).

## [0.3.0] — 2026-06-27

### Added

- **Stable cross-artifact bank IDs** (egm-contracts v0.5.0 / egm-data v0.4.0). Every bank
  carries an egm-contracts `ArtifactId`, auto-derived from the bank's role at write time
  (`tbank_` / `ptbank_` / `nbank_` + `iafdb` + UTC date) or overridden via `data.bank_id`.
  The `iafdb_bank` stamps the id on its HDF5 root attr (schema 1.2) and propagates it onto
  the paired ClassifierBank; the noise bank's id rides on the `noise_bank_run_record.json`
  sidecar (schema 1.1). Derivation + validation live in `ids.py`.

### Dependencies

Re-pins `egm-contracts v0.5.1`, `egm-data v0.4.0`.

## [0.2.1] — 2026-06-20

### Changed

- Cascade to `egm-data` / `egm-contracts` v0.3.0 (schema renames).

## [0.2.0] — 2026-06-18

First real release after python-template personalization: the PhysioNet IAFDB ingestion +
producer pipeline.

### Added

- **Download + load** — `iafdb-download` (32 records, 8 patients × 4 placements from
  PhysioNet), a typed `IAFDBRecord` dataclass with the WFDB loader, and the `iter_records`
  walker.
- **Bank producer** (`iafdb-export-bank`) — R-wave-anchored calibration, three threshold
  modes (absolute / percentile / none), 512 ms / 256 ms default windowing, and a
  ClassifierBank secondary output with a consumer-supplied label policy.
- **Noise-bank producer** (`iafdb-export-noise-bank`) — scale-invariant percentile default
  (+ absolute-mV alternate), 200 ms / 100 ms default windowing, and a JSON provenance
  sidecar with optional per-trace audit columns.
- **`iafdb-inspect`** interactive metadata + per-channel stats printer.
- YAML-config-driven CLIs + six annotated example configs. All HDF5 I/O via
  `myocard-egm-data`; all DSP via `myocard-egm-signal`.

### Dependencies

Pins `egm-contracts v0.2.0`, `egm-data v0.2.0`, `egm-signal v0.1.0`.

[Unreleased]: https://github.com/myocard-labs/iafdb-pipeline/compare/v0.3.0...HEAD
[0.3.0]: https://github.com/myocard-labs/iafdb-pipeline/releases/tag/v0.3.0
[0.2.1]: https://github.com/myocard-labs/iafdb-pipeline/releases/tag/v0.2.1
[0.2.0]: https://github.com/myocard-labs/iafdb-pipeline/releases/tag/v0.2.0
