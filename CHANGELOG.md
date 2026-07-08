# Changelog

All notable changes to `iafdb-pipeline` are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); the project aims to follow
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- **`unlabeled` label policy** (`format.label_policy: unlabeled`) — the `label_fn` returns
  `None`, leaving every `label_truth` unset; the honest IAFDB policy that feeds an unlabeled
  `upred_` eval (IAFDB has no fibrosis ground truth).
- **Fail-fast `bank_id` validation** — a malformed `data.bank_id` override is now rejected at
  config-load, before the export run, instead of at the write step (Refactor Step 8).

### Changed

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
